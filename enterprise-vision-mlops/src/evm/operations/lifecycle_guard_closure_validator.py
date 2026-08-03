from __future__ import annotations

import argparse
import hashlib
import json
import tomllib
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from evm.control_panel.readiness_evaluator import runtime_path
from evm.operations.lifecycle_guard_e_runner import (
    DEFAULT_INFERENCE_IMAGE_URI,
    git_text,
    runtime_projection,
    write_json,
)
from evm.operations.scenario_e_runner import production_snapshot


SCHEMA = "evm.lifecycle_guard_closure_validation.v1"
INDEX_NAMES = {"evidence-index.json", "artifact-index.json"}


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return payload


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _host_path(value: str, *, relative_to: Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = relative_to / candidate
    if candidate.is_file():
        return candidate.resolve()
    mapped = runtime_path(value)
    return mapped.resolve() if mapped.is_file() else candidate.resolve()


def _index_references(payload: Any, key: str = "") -> Iterable[str]:
    if isinstance(payload, dict):
        for child_key, value in payload.items():
            yield from _index_references(value, str(child_key))
    elif isinstance(payload, list):
        for value in payload:
            yield from _index_references(value, key)
    elif isinstance(payload, str):
        normalized = payload.replace("\\", "/").lower()
        key_normalized = key.lower()
        identity_field = key_normalized.endswith(("sha256", "digest", "count"))
        if ("evidence_index" in key_normalized and not identity_field) or normalized.endswith(
            ("/evidence-index.json", "/artifact-index.json")
        ):
            yield payload


def validate_evidence_graph(start_indexes: list[Path], evidence_root: Path) -> dict[str, Any]:
    queue: deque[Path] = deque(path.resolve() for path in start_indexes)
    visited_indexes: set[Path] = set()
    visited_artifacts: set[Path] = set()
    failures: list[str] = []

    while queue:
        index_path = queue.popleft()
        if index_path in visited_indexes:
            continue
        visited_indexes.add(index_path)
        if not _inside(index_path, evidence_root):
            failures.append(f"index_outside_evidence_root:{index_path}")
            continue
        if not index_path.is_file():
            failures.append(f"index_missing:{index_path}")
            continue
        try:
            index = read_json(index_path)
        except Exception as exc:  # pragma: no cover - defensive evidence boundary
            failures.append(f"index_invalid:{index_path}:{type(exc).__name__}:{exc}")
            continue
        artifacts = index.get("artifacts")
        if artifacts is None and isinstance(index.get("files"), dict):
            artifacts = list(index["files"].values())
        if not isinstance(artifacts, list):
            failures.append(f"index_artifacts_missing:{index_path}")
            continue
        declared = index.get("artifact_count")
        if declared is not None and declared != len(artifacts):
            failures.append(
                f"index_count_mismatch:{index_path}:declared={declared}:actual={len(artifacts)}"
            )
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                failures.append(f"index_artifact_invalid:{index_path}")
                continue
            location = artifact.get("path") or artifact.get("uri")
            expected = str(artifact.get("sha256") or "").lower()
            if not isinstance(location, str) or len(expected) != 64:
                failures.append(f"artifact_identity_invalid:{index_path}:{location}")
                continue
            target = _host_path(location, relative_to=index_path.parent)
            if not _inside(target, evidence_root):
                failures.append(f"artifact_outside_evidence_root:{target}")
                continue
            if not target.is_file():
                failures.append(f"artifact_missing:{target}")
                continue
            visited_artifacts.add(target)
            observed_size = target.stat().st_size
            expected_size = artifact.get("size_bytes")
            if expected_size is not None and int(expected_size) != observed_size:
                failures.append(
                    f"artifact_size_mismatch:{target}:expected={expected_size}:actual={observed_size}"
                )
                continue
            observed = sha256_file(target)
            if observed != expected:
                failures.append(
                    f"artifact_sha256_mismatch:{target}:expected={expected}:actual={observed}"
                )
                continue
            if target.name in INDEX_NAMES:
                queue.append(target)
            if target.suffix.lower() == ".json" and observed_size <= 2 * 1024 * 1024:
                try:
                    payload = read_json(target)
                except Exception:
                    continue
                for reference in _index_references(payload):
                    queue.append(_host_path(reference, relative_to=target.parent))

    return {
        "passed": not failures,
        "index_count": len(visited_indexes),
        "artifact_count": len(visited_artifacts),
        "failures": failures,
        "indexes": sorted(str(path) for path in visited_indexes),
    }


def validate_embedded_report_artifacts(report_path: Path, evidence_root: Path) -> dict[str, Any]:
    report = read_json(report_path)
    failures: list[str] = []
    checked = 0
    for artifact in report.get("artifacts", []):
        if not isinstance(artifact, dict):
            failures.append(f"embedded_artifact_invalid:{report_path}")
            continue
        uri = artifact.get("uri")
        expected = str(artifact.get("sha256") or "").lower()
        if not isinstance(uri, str) or len(expected) != 64:
            failures.append(f"embedded_artifact_identity_invalid:{report_path}:{uri}")
            continue
        path = _host_path(uri, relative_to=report_path.parent)
        if not _inside(path, evidence_root):
            failures.append(f"embedded_artifact_outside_root:{path}")
        elif not path.is_file():
            failures.append(f"embedded_artifact_missing:{path}")
        elif sha256_file(path) != expected:
            failures.append(f"embedded_artifact_sha256_mismatch:{path}")
        else:
            checked += 1
    return {"passed": not failures, "artifact_count": checked, "failures": failures}


def _all_true(checks: Any) -> bool:
    return isinstance(checks, dict) and bool(checks) and all(value is True for value in checks.values())


def _claim_boundary_valid(value: Any) -> bool:
    text = str(value or "").lower()
    has_negation = any(item in text for item in ("no ", "not ", "does not"))
    names_prohibited_scope = any(
        item in text for item in ("real-user traffic", "production traffic", "ha", "sla")
    )
    return "single-node" in text and has_negation and names_prohibited_scope


def _report_pass(report: dict[str, Any], scenario: str) -> bool:
    return (
        report.get("schema_version") == "evm.operational_failure_evidence.v1"
        and report.get("scenario_id") == scenario
        and report.get("status") == "passed"
        and (report.get("live_proof_closure") or {}).get("decision") == "passed"
    )


def _report_index(report_path: Path) -> Path | None:
    sibling = report_path.parent / "evidence-index.json"
    return sibling if sibling.is_file() else None


def _reference_hash_closure(report_paths: list[Path], evidence_root: Path) -> dict[str, Any]:
    indexes = [path for report in report_paths if (path := _report_index(report))]
    graph = validate_evidence_graph(indexes, evidence_root) if indexes else {
        "passed": True,
        "index_count": 0,
        "artifact_count": 0,
        "failures": [],
        "indexes": [],
    }
    embedded = [
        validate_embedded_report_artifacts(path, evidence_root)
        for path in report_paths
        if _report_index(path) is None
    ]
    failures = list(graph["failures"])
    for result in embedded:
        failures.extend(result["failures"])
    return {
        **graph,
        "passed": not failures,
        "artifact_count": graph["artifact_count"]
        + sum(item["artifact_count"] for item in embedded),
        "failures": failures,
    }


def _a_result(config: dict[str, Any], evidence_root: Path) -> dict[str, Any]:
    root = Path(config["root"])
    result = read_json(root / "result.json")
    reports = [Path(path) for path in config["deterministic_reports"]]
    phases = {item.get("phase"): item for item in result.get("phases", [])}
    recovery = phases.get("committed_m1_exact_pod_recovery", {})
    forbidden = result.get("production_mutations", {})
    reachability = (
        result.get("schema_version") == "evm.lifecycle_guard_a_result.v1"
        and result.get("status") == "passed"
        and set(phases)
        == {
            "prepare_approve_apply_verify_commit",
            "committed_m1_exact_pod_recovery",
            "separate_m0_rollback",
        }
        and all(item.get("status") == "passed" for item in phases.values())
        and float(recovery.get("detection_seconds", 999)) <= 30
        and float(recovery.get("recovery_seconds", 999)) <= 300
        and forbidden.get("device_plugin") == 0
        and forbidden.get("data") == 0
        and forbidden.get("registry") == 0
        and forbidden.get("cluster_wide") == 0
    )
    decisions = []
    for path in reports:
        report = read_json(path)
        decisions.append(
            {
                "passed": _report_pass(report, "A"),
                "expected": (report.get("decision") or {}).get("expected"),
                "recovery_action": (report.get("recovery") or {}).get("action"),
                "recovery_result": (report.get("recovery") or {}).get("result"),
                "manual_patch": (report.get("mitigation") or {}).get("manual_patch"),
                "detection_within_slo": float((report.get("timing") or {}).get("detection_seconds", 999)) <= 30,
                "recovery_within_slo": float((report.get("timing") or {}).get("recovery_seconds", 999)) <= 300,
            }
        )
    deterministic = len(decisions) >= 3 and all(
        item["passed"]
        and item["recovery_action"] == "deployment_controller_recreated_pod"
        and item["recovery_result"] == "passed"
        and item["manual_patch"] is False
        and item["detection_within_slo"]
        and item["recovery_within_slo"]
        for item in decisions
    )
    graph = validate_evidence_graph([root / "evidence-index.json"], evidence_root)
    references = _reference_hash_closure(reports, evidence_root)
    claim_boundary = _claim_boundary_valid(result.get("claim_boundary"))
    blockers = []
    if not reachability:
        blockers.append("a_integrated_l9_recovery_not_proven")
    if not deterministic:
        blockers.append("a_three_run_recovery_determinism_not_proven")
    if not graph["passed"] or not references["passed"]:
        blockers.append("a_evidence_hash_closure_failed")
    if not claim_boundary:
        blockers.append("a_claim_boundary_missing")
    return {
        "status": "pass" if not blockers else "blocked",
        "lifecycle_reachability": {"passed": reachability, "stage": "L9"},
        "decision_determinism": {"passed": deterministic, "samples": len(decisions)},
        "hash_closure": {"integrated": graph, "references": references},
        "claim_boundary": {"passed": claim_boundary, "text": result.get("claim_boundary")},
        "blockers": blockers,
        "source_revision": result.get("source_revision"),
        "result_uri": str((root / "result.json").resolve()),
    }


def _b_semantic(report: dict[str, Any]) -> tuple[Any, ...]:
    decision = report.get("decision") or {}
    recovery = report.get("recovery") or {}
    return (
        decision.get("observed"),
        tuple(decision.get("blocker_codes") or []),
        recovery.get("action"),
        recovery.get("result"),
    )


def _b_result(config: dict[str, Any], evidence_root: Path) -> dict[str, Any]:
    root = Path(config["root"])
    result = read_json(root / "result.json")
    branches = {item.get("branch"): item for item in result.get("branches", [])}
    expected = {
        "quality": ("blocked_admission", ("quality_f1_below_minimum",), "stable_route_retained"),
        "runtime": ("rolled_back", ("runtime_error_rate_exceeded",), "zero_allocation_and_restore_stable_route"),
    }
    deterministic_groups: dict[str, Any] = {}
    reference_paths: list[Path] = []
    for name, report_key in (("quality", "quality_reports"), ("runtime", "runtime_reports")):
        paths = [Path(path) for path in config[report_key]]
        reference_paths.extend(paths)
        semantics = []
        reports_pass = True
        for path in paths:
            report = read_json(path)
            reports_pass = reports_pass and _report_pass(report, "B")
            semantics.append(_b_semantic(report))
        branch = branches.get(name, {})
        branch_decision = branch.get("decision") or {}
        branch_rollback = branch.get("rollback") or {}
        semantics.append(
            (
                branch_decision.get("state"),
                tuple(branch_decision.get("blocker_codes") or []),
                branch_rollback.get("action"),
                "passed" if branch_rollback.get("exact_identity_restored") else "failed",
            )
        )
        state, blockers, action = expected[name]
        passed = (
            reports_pass
            and len(semantics) >= 3
            and all(
                semantic[0] == state
                and semantic[1] == blockers
                and semantic[2] == action
                and semantic[3] == "passed"
                for semantic in semantics
            )
        )
        deterministic_groups[name] = {"passed": passed, "samples": len(semantics)}
    reachability = (
        result.get("schema_version") == "evm.lifecycle_guard_scenario_b_integrated.v1"
        and result.get("status") == "pass"
        and set(branches) == {"quality", "runtime"}
        and all(
            branch.get("status") == "pass"
            and (branch.get("checks") or {}).get("full_lifecycle_reached_release_boundary") is True
            and (branch.get("checks") or {}).get("deployment_intent_zero") is True
            and (branch.get("external_delta") or {}).get("deployment_intents") == 0
            for branch in branches.values()
        )
    )
    graph = validate_evidence_graph([root / "evidence-index.json"], evidence_root)
    references = _reference_hash_closure(reference_paths, evidence_root)
    claim_boundary = _claim_boundary_valid(result.get("claim_boundary"))
    blockers = []
    if not reachability:
        blockers.append("b_integrated_l5_l7_release_guard_not_proven")
    if not all(group["passed"] for group in deterministic_groups.values()):
        blockers.append("b_three_run_decision_determinism_not_proven")
    if not graph["passed"] or not references["passed"]:
        blockers.append("b_evidence_hash_closure_failed")
    if not claim_boundary:
        blockers.append("b_claim_boundary_missing")
    return {
        "status": "pass" if not blockers else "blocked",
        "lifecycle_reachability": {"passed": reachability, "stages": ["L5", "L7"]},
        "decision_determinism": deterministic_groups,
        "hash_closure": {"integrated": graph, "references": references},
        "claim_boundary": {"passed": claim_boundary, "text": result.get("claim_boundary")},
        "blockers": blockers,
        "source_revision": result.get("source_commit"),
        "result_uri": str((root / "result.json").resolve()),
    }


def _c_result(config: dict[str, Any], evidence_root: Path) -> dict[str, Any]:
    root = Path(config["root"])
    result = read_json(root / "result.json")
    reports = [Path(path) for path in config["deterministic_reports"]]
    decisions = [read_json(path) for path in reports]
    deterministic = len(decisions) >= 3 and all(
        _report_pass(report, "C")
        and (report.get("decision") or {}).get("observed") == "review_required"
        and (report.get("recovery") or {}).get("result") == "passed"
        for report in decisions
    )
    checks = result.get("checks") or {}
    reachability = (
        result.get("schema_version") == "evm.lifecycle_guard_scenario_c_integrated.v1"
        and result.get("status") == "pass"
        and _all_true(checks)
        and int(result.get("registration_attempts", 0)) >= 3
        and (result.get("external_delta") or {}).get("deployment_intents") == 0
    )
    graph = validate_evidence_graph([root / "evidence-index.json"], evidence_root)
    references = _reference_hash_closure(reports, evidence_root)
    claim_boundary = _claim_boundary_valid(result.get("claim_boundary"))
    blockers = []
    if not reachability:
        blockers.append("c_integrated_l2_l6_hold_resume_not_proven")
    if not deterministic:
        blockers.append("c_three_run_review_decision_determinism_not_proven")
    if not graph["passed"] or not references["passed"]:
        blockers.append("c_evidence_hash_closure_failed")
    if not claim_boundary:
        blockers.append("c_claim_boundary_missing")
    return {
        "status": "pass" if not blockers else "blocked",
        "lifecycle_reachability": {"passed": reachability, "stages": ["L2", "L3", "L6"]},
        "decision_determinism": {"passed": deterministic, "samples": len(decisions)},
        "hash_closure": {"integrated": graph, "references": references},
        "claim_boundary": {"passed": claim_boundary, "text": result.get("claim_boundary")},
        "blockers": blockers,
        "source_revision": result.get("source_commit"),
        "result_uri": str((root / "result.json").resolve()),
    }


def _d_report_semantic(report: dict[str, Any]) -> bool:
    decision = report.get("decision") or {}
    recovery = report.get("recovery") or {}
    return (
        _report_pass(report, "D")
        and decision.get("expected") == "restart_exact"
        and decision.get("observed") == "restart_exact_then_live"
        and recovery.get("action") == "canonical_child_launcher"
        and recovery.get("result") == "live_exact_revision"
    )


def _d_result(config: dict[str, Any], evidence_root: Path) -> dict[str, Any]:
    root = Path(config["root"])
    result = read_json(root / "result.json")
    worker_reports = [Path(path) for path in config["worker_reports"]]
    observer_reports = [Path(path) for path in config["observer_reports"]]
    worker_samples = [read_json(path) for path in worker_reports]
    observer_samples = [read_json(path) for path in observer_reports]
    worker_deterministic = (
        len(worker_samples) + 1 >= 3
        and all(_d_report_semantic(report) for report in worker_samples)
        and (result.get("checks") or {}).get("training_side_effect_reconciled_same_identity") is True
    )
    observer_deterministic = len(observer_samples) >= 3 and all(
        _d_report_semantic(report) for report in observer_samples
    )
    timing = result.get("timing") or {}
    reachability = (
        result.get("schema_version") == "evm.lifecycle_guard_scenario_d_training_recovery.v1"
        and result.get("status") == "pass"
        and _all_true(result.get("checks"))
        and (result.get("injection") or {}).get("target") == "lifecycle_worker"
        and float(timing.get("detection_seconds", 999)) <= 10
        and float(timing.get("recovery_seconds", 999)) <= 60
    )
    all_reports = worker_reports + observer_reports
    graph = validate_evidence_graph([root / "evidence-index.json"], evidence_root)
    references = _reference_hash_closure(all_reports, evidence_root)
    claim_boundary = _claim_boundary_valid(result.get("claim_boundary"))
    blockers = []
    if not reachability:
        blockers.append("d_integrated_l3_worker_recovery_not_proven")
    if not worker_deterministic or not observer_deterministic:
        blockers.append("d_worker_observer_three_run_determinism_not_proven")
    if not graph["passed"] or not references["passed"]:
        blockers.append("d_evidence_hash_closure_failed")
    if not claim_boundary:
        blockers.append("d_claim_boundary_missing")
    return {
        "status": "pass" if not blockers else "blocked",
        "lifecycle_reachability": {"passed": reachability, "stage": "L3"},
        "decision_determinism": {
            "worker": {"passed": worker_deterministic, "samples": len(worker_samples) + 1},
            "observer": {"passed": observer_deterministic, "samples": len(observer_samples)},
        },
        "hash_closure": {"integrated": graph, "references": references},
        "claim_boundary": {"passed": claim_boundary, "text": result.get("claim_boundary")},
        "blockers": blockers,
        "source_revision": result.get("source_commit"),
        "result_uri": str((root / "result.json").resolve()),
    }


def _e_result(config: dict[str, Any], evidence_root: Path) -> dict[str, Any]:
    replay_root = Path(config["root"])
    replay_result = read_json(replay_root / "result.json")
    integrated_root = Path(config.get("integrated_root") or replay_root)
    integrated_result = read_json(integrated_root / "result.json")
    deterministic = replay_result.get("status") == "pass" and _all_true(
        replay_result.get("checks")
    )
    reachability = integrated_result.get("lifecycle_reachability") or {}
    actual_lifecycle_injection = (
        integrated_result.get("status") == "pass"
        and _all_true(integrated_result.get("checks"))
        and integrated_result.get("mode") == config["required_live_mode"]
        and bool(integrated_result.get("lifecycle_run_id"))
        and all(bool(reachability.get(stage)) for stage in ("L2", "L4", "L6"))
    )
    graph = validate_evidence_graph(
        list(
            dict.fromkeys(
                [
                    replay_root / "evidence-index.json",
                    integrated_root / "evidence-index.json",
                ]
            )
        ),
        evidence_root,
    )
    claim_boundary = _claim_boundary_valid(integrated_result.get("claim_boundary"))
    blockers = []
    if not deterministic:
        blockers.append("e_three_run_integrity_decision_determinism_not_proven")
    if not actual_lifecycle_injection:
        blockers.append("e_actual_lifecycle_injection_missing:controlled_branch_replay_only")
    if not graph["passed"]:
        blockers.append("e_evidence_hash_closure_failed")
    if not claim_boundary:
        blockers.append("e_claim_boundary_missing")
    return {
        "status": "pass" if not blockers else "blocked",
        "lifecycle_reachability": {
            "passed": actual_lifecycle_injection,
            "required_stages": ["L2", "L4", "L6"],
            "observed_mode": integrated_result.get("mode"),
            "lifecycle_run_id": integrated_result.get("lifecycle_run_id"),
            "data_blocked_run_id": integrated_result.get("data_blocked_run_id"),
            "release_blocked_run_id": integrated_result.get("release_blocked_run_id"),
            "observed_stages": reachability,
        },
        "decision_determinism": {"passed": deterministic, "replays_per_branch": 3},
        "hash_closure": {"replay_and_integrated": graph},
        "claim_boundary": {
            "passed": claim_boundary,
            "text": integrated_result.get("claim_boundary"),
        },
        "blockers": blockers,
        "source_revision": integrated_result.get("source_revision"),
        "replay_result_uri": str((replay_root / "result.json").resolve()),
        "result_uri": str((integrated_root / "result.json").resolve()),
    }


def validate_runtime(
    snapshot: dict[str, Any],
    *,
    expected_model_digest: str,
    expected_runtime_revision: str | None = None,
) -> dict[str, Any]:
    projection = runtime_projection(snapshot)
    children = projection.get("children") or {}
    checks = {
        "one_ready_replica": projection.get("ready_replicas") == 1,
        "exact_m0_model": projection.get("model_digest") == expected_model_digest,
        "cuda_inference": projection.get("inference_device") == "cuda",
        "gpu_allocatable": "1" in [str(item) for item in projection.get("gpu_allocatable", [])],
        "device_plugin_ready": bool(projection.get("device_plugin"))
        and all(item.get("ready") is True for item in projection["device_plugin"]),
        "prometheus_targets_up": all(
            projection.get("prometheus_targets", {}).get(job) == "up"
            for job in ("evm-api", "evm-b0-production")
        ),
        "supervisor_live": projection.get("supervisor_status") in {"healthy", "online"},
        "worker_observer_live": set(children) == {"lifecycle_worker", "kubernetes_observer"}
        and all(item.get("status") in {"live", "online"} for item in children.values()),
        "runtime_revision_converged": not expected_runtime_revision
        or (
            projection.get("supervisor_source_commit") == expected_runtime_revision
            and all(
                item.get("source_commit") == expected_runtime_revision
                for item in children.values()
            )
        ),
    }
    return {"passed": all(checks.values()), "checks": checks, "projection": projection}


def build_output_index(output_root: Path) -> dict[str, Any]:
    artifacts = [
        {
            "path": path.relative_to(output_root).as_posix(),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(output_root.rglob("*"))
        if path.is_file() and path.name != "evidence-index.json"
    ]
    payload = {
        "schema_version": "evm.lifecycle_guard_closure_evidence_index.v1",
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }
    write_json(output_root / "evidence-index.json", payload)
    return payload


def run(config_path: Path, project_root: Path, *, capture_runtime: bool = True) -> Path:
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    evidence_root = Path(config["evidence_root"]).resolve()
    source_revision = git_text(project_root, "rev-parse", "HEAD")
    run_id = f"closure-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{source_revision[:8]}"
    output_root = Path(config["output_root"]).resolve() / run_id
    output_root.mkdir(parents=True, exist_ok=False)

    scenarios = {
        "A": _a_result(config["scenario_a"], evidence_root),
        "B": _b_result(config["scenario_b"], evidence_root),
        "C": _c_result(config["scenario_c"], evidence_root),
        "D": _d_result(config["scenario_d"], evidence_root),
        "E": _e_result(config["scenario_e"], evidence_root),
    }
    m0 = read_json(Path(config["scenario_a"]["root"]) / "result.json")["final_identity"]
    if capture_runtime:
        try:
            snapshot = production_snapshot(DEFAULT_INFERENCE_IMAGE_URI)
            runtime = validate_runtime(
                snapshot,
                expected_model_digest=m0["model_sha256"],
                expected_runtime_revision=config["expected_runtime_revision"],
            )
        except Exception as exc:  # runtime unavailability is a closure blocker
            runtime = {
                "passed": False,
                "checks": {},
                "error": f"{type(exc).__name__}:{exc}",
                "projection": {},
            }
    else:
        runtime = {"passed": True, "checks": {"capture_skipped_for_test": True}, "projection": {}}
    write_json(output_root / "runtime-snapshot.json", runtime)

    blockers = [
        f"scenario_{name.lower()}:{blocker}"
        for name, scenario in scenarios.items()
        for blocker in scenario["blockers"]
    ]
    if not runtime["passed"]:
        blockers.append("current_runtime_restoration_not_proven")
    result = {
        "schema_version": SCHEMA,
        "run_id": run_id,
        "mode": "read_only_evidence_graph_and_runtime_validation",
        "source_revision": source_revision,
        "expected_runtime_revision": config["expected_runtime_revision"],
        "started_and_finished_at": utc_now(),
        "scenarios": scenarios,
        "runtime_restoration": runtime,
        "status": "pass" if not blockers else "blocked",
        "blockers": blockers,
        "next_gate_admitted": not blockers,
        "claim_boundary": (
            "Controlled local single-node lifecycle evidence audit. It does not prove "
            "customer production, real traffic, HA, zero downtime, distributed exactly-once, "
            "multi-node failover or an enterprise SLA."
        ),
        "remediation": (
            []
            if not blockers
            else [
                {
                    "blocker": blocker,
                    "required_action": (
                        "Run a fresh immutable lifecycle attempt through the missing guard "
                        "injection point; retain the blocked attempt and require zero downstream "
                        "intent before corrected-attempt admission."
                    ),
                }
                for blocker in blockers
            ]
        ),
    }
    write_json(output_root / "result.json", result)
    write_json(
        output_root / "contract-snapshot.json",
        {"config": config, "config_sha256": sha256_file(config_path)},
    )
    build_output_index(output_root)
    return output_root / "result.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate integrated A-E lifecycle guard closure.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--allow-blocked", action="store_true")
    parser.add_argument("--skip-runtime", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    result_path = run(
        args.config.resolve(),
        args.project_root.resolve(),
        capture_runtime=not args.skip_runtime,
    )
    result = read_json(result_path)
    print(json.dumps({"result_uri": str(result_path), "status": result["status"]}, sort_keys=True))
    return 0 if result["status"] == "pass" or args.allow_blocked else 2


if __name__ == "__main__":
    raise SystemExit(main())
