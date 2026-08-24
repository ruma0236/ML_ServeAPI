from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evm.control_panel.transactional_store import canonical_digest
from evm.scale_validation.s1_runtime import sha256_file
from evm.scale_validation.s8_evidence import (
    S8EvidenceValidationError,
    canonical,
    validate_s8_experiment,
)
from evm.scale_validation.s8_runtime import S8RuntimeConfig, git_blob_identity


SMOKE_SCHEMA_VERSION = "evm.s8_current_revision_runtime_smoke.v1"
REGRESSION_SCHEMA_VERSION = "evm.s8_closure_regression.v1"
CLOSURE_SCHEMA_VERSION = "evm.s8_dependency_soak_closure.v1"
PRIVATE_CLOSURE_INDEX_SCHEMA_VERSION = "evm.s8_private_closure_index.v1"

EXPERIMENT_PATH = Path("docs/status/evidence/s8-dependency-soak-experiment.json")
SMOKE_PATH = Path("docs/status/evidence/s8-current-revision-runtime-smoke.json")
REGRESSION_PATH = Path("docs/status/evidence/s8-closure-regression-evidence.json")
CLOSURE_PATH = Path("docs/status/evidence/s8-dependency-soak-closure.json")
REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")

REQUIRED_SUITE_IDS = (
    "changed_file_lint",
    "focused_s8_closure",
    "real_postgresql",
    "lifecycle_host_e2e",
    "s0_s8_status_evidence",
    "full_python",
    "control_panel",
    "frontend_production_build",
)

SMOKE_SOURCE_PATHS = {
    "runner": Path("scripts/dev/run_s8_current_revision_smoke.py"),
    "closure": Path("src/evm/scale_validation/s8_closure.py"),
    "runtime": Path("src/evm/scale_validation/s8_runtime.py"),
    "serving": Path("apps/api/efficientnet_serving.py"),
    "serving_manifest": Path(
        "infra/kubernetes/expedited-production-validation/production/serving.yaml"
    ),
}


class S8ClosureValidationError(RuntimeError):
    pass


def validate_s8_runtime_smoke(
    payload: Mapping[str, Any],
    *,
    private_closure_root: Path,
    project_root: Path,
    validation_revision: str = "HEAD",
) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema_version") != SMOKE_SCHEMA_VERSION:
        errors.append("schema_version")
    if payload.get("status") != "verified" or payload.get("verdict") != "passed":
        errors.append("status")
    if payload.get("acceptance_credit") is not False:
        errors.append("acceptance_credit")
    source = _mapping(payload.get("source_identity"))
    revision = str(source.get("revision") or "")
    if not REVISION_PATTERN.fullmatch(revision):
        errors.append("source_revision")
    elif not _is_ancestor(project_root, revision, validation_revision):
        errors.append("source_revision_ancestry")
    if REVISION_PATTERN.fullmatch(revision):
        recorded_blobs = _mapping(source.get("git_blobs"))
        for label, relative_path in SMOKE_SOURCE_PATHS.items():
            try:
                expected = git_blob_identity(project_root, revision, relative_path)
            except (OSError, subprocess.SubprocessError):
                errors.append(f"source_blob_unresolvable:{label}")
                continue
            if canonical(recorded_blobs.get(label)) != canonical(expected):
                errors.append(f"source_blob_identity:{label}")

    private = _mapping(payload.get("private_evidence"))
    private_path = private_closure_root / str(private.get("path") or "")
    raw: dict[str, Any] = {}
    if not private_path.is_file():
        errors.append("private_smoke_missing")
    else:
        if private_path.stat().st_size != int(private.get("bytes", -1)):
            errors.append("private_smoke_bytes")
        if sha256_file(private_path) != private.get("sha256"):
            errors.append("private_smoke_sha256")
        raw = _read_mapping(private_path)

    checks = _mapping(payload.get("checks"))
    if raw and canonical(checks) != canonical(raw.get("checks")):
        errors.append("smoke_projection")
    expected_checks = {
        "api_healthy": True,
        "queue_worker_healthy": True,
        "control_plane_postgresql_healthy": True,
        "source_serving_ready": True,
        "target_serving_scaled_zero": True,
        "real_cuda_inference": True,
        "prometheus_all_targets_up": True,
        "queue_active_zero": True,
        "queue_leased_zero": True,
        "queue_outcome_unknown_zero": True,
        "s8_processes_removed": True,
        "s8_containers_removed": True,
        "worker_metrics_port_available": True,
    }
    for key, expected in expected_checks.items():
        if checks.get(key) is not expected:
            errors.append(f"smoke_check:{key}")
    if int(checks.get("prometheus_targets_total", 0)) < 1:
        errors.append("prometheus_targets_total")
    if checks.get("prometheus_targets_up") != checks.get("prometheus_targets_total"):
        errors.append("prometheus_targets_up")
    if int(checks.get("historical_terminal_serving_pods", -1)) < 0:
        errors.append("historical_terminal_serving_pods")
    if errors:
        raise S8ClosureValidationError(
            "s8_runtime_smoke_invalid:" + ",".join(sorted(set(errors)))
        )
    return {
        "valid": True,
        "revision": revision,
        "checks": checks,
        "private_smoke_sha256": private["sha256"],
    }


def validate_s8_regression_evidence(
    payload: Mapping[str, Any],
    *,
    private_closure_root: Path,
    project_root: Path,
    validation_revision: str = "HEAD",
) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema_version") != REGRESSION_SCHEMA_VERSION:
        errors.append("schema_version")
    if payload.get("status") != "passed":
        errors.append("status")
    source = _mapping(payload.get("source_identity"))
    revision = str(source.get("revision") or "")
    if not REVISION_PATTERN.fullmatch(revision):
        errors.append("source_revision")
    elif not _is_ancestor(project_root, revision, validation_revision):
        errors.append("source_revision_ancestry")
    suites = [_mapping(item) for item in _sequence(payload.get("suites"))]
    suite_ids = tuple(str(item.get("suite_id")) for item in suites)
    if suite_ids != REQUIRED_SUITE_IDS:
        errors.append("suite_identity")
    total_tests = 0
    total_skipped = 0
    for suite in suites:
        suite_id = str(suite.get("suite_id"))
        log_path = private_closure_root / str(suite.get("log_path") or "")
        meta_path = private_closure_root / str(suite.get("metadata_path") or "")
        if not log_path.is_file():
            errors.append(f"regression_log_missing:{suite_id}")
            continue
        if not meta_path.is_file():
            errors.append(f"regression_metadata_missing:{suite_id}")
            continue
        if log_path.stat().st_size != int(suite.get("log_bytes", -1)):
            errors.append(f"regression_log_bytes:{suite_id}")
        if sha256_file(log_path) != suite.get("log_sha256"):
            errors.append(f"regression_log_sha256:{suite_id}")
        if meta_path.stat().st_size != int(suite.get("metadata_bytes", -1)):
            errors.append(f"regression_metadata_bytes:{suite_id}")
        if sha256_file(meta_path) != suite.get("metadata_sha256"):
            errors.append(f"regression_metadata_sha256:{suite_id}")
        meta = _read_mapping(meta_path)
        if meta.get("suite_id") != suite_id:
            errors.append(f"regression_metadata_suite:{suite_id}")
        if meta.get("source_revision") != revision:
            errors.append(f"regression_metadata_revision:{suite_id}")
        if int(meta.get("exit_code", -1)) != 0:
            errors.append(f"regression_exit_code:{suite_id}")
        if suite.get("command") != meta.get("public_command"):
            errors.append(f"regression_command:{suite_id}")
        observed = _derive_regression_counts(suite_id, log_path.read_text(encoding="utf-8"))
        if canonical(suite.get("observed")) != canonical(observed):
            errors.append(f"regression_count_projection:{suite_id}")
        if suite.get("status") != "passed" or not observed.get("passed"):
            errors.append(f"regression_not_passed:{suite_id}")
        total_tests += int(observed.get("tests_passed", 0))
        total_skipped += int(observed.get("tests_skipped", 0))
    summary = {
        "suite_count": len(suites),
        "tests_passed": total_tests,
        "tests_skipped": total_skipped,
        "all_exit_codes_zero": not any(
            error.startswith("regression_exit_code") for error in errors
        ),
    }
    if canonical(payload.get("summary")) != canonical(summary):
        errors.append("regression_summary")
    if errors:
        raise S8ClosureValidationError(
            "s8_regression_invalid:" + ",".join(sorted(set(errors)))
        )
    return {"valid": True, "revision": revision, **summary}


def build_private_closure_index(root: Path, *, generated_at: str) -> dict[str, Any]:
    entries = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "private-closure-index.json":
            continue
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return {
        "schema_version": PRIVATE_CLOSURE_INDEX_SCHEMA_VERSION,
        "generated_at": generated_at,
        "artifact_count": len(entries),
        "aggregate_sha256": canonical_digest(entries),
        "artifacts": entries,
    }


def validate_private_closure_index(
    root: Path, *, public_summary: Mapping[str, Any]
) -> dict[str, Any]:
    index_path = root / "private-closure-index.json"
    if not index_path.is_file():
        raise S8ClosureValidationError("s8_private_closure_index_missing")
    payload = _read_mapping(index_path)
    if payload.get("schema_version") != PRIVATE_CLOSURE_INDEX_SCHEMA_VERSION:
        raise S8ClosureValidationError("s8_private_closure_index_schema")
    expected = build_private_closure_index(root, generated_at=str(payload.get("generated_at")))
    if canonical(payload) != canonical(expected):
        raise S8ClosureValidationError("s8_private_closure_index_projection")
    projection = {
        "artifact_count": expected["artifact_count"],
        "aggregate_sha256": expected["aggregate_sha256"],
        "index_sha256": sha256_file(index_path),
    }
    if canonical(public_summary) != canonical(projection):
        raise S8ClosureValidationError("s8_private_closure_public_projection")
    return projection


def validate_s8_closure(
    payload: Mapping[str, Any],
    *,
    experiment_private_root: Path,
    private_closure_root: Path,
    project_root: Path,
    config: S8RuntimeConfig,
    validation_revision: str = "HEAD",
) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema_version") != CLOSURE_SCHEMA_VERSION:
        errors.append("schema_version")
    if payload.get("status") != "verified" or payload.get("verdict") != "passed":
        errors.append("status")
    source = _mapping(payload.get("source_identity"))
    support_revision = str(source.get("supporting_revision") or "")
    if not REVISION_PATTERN.fullmatch(support_revision):
        errors.append("supporting_revision")
    elif not _is_ancestor(project_root, support_revision, validation_revision):
        errors.append("supporting_revision_ancestry")
    supporting = _mapping(source.get("supporting_artifacts"))
    expected_paths = {
        "experiment": EXPERIMENT_PATH,
        "runtime_smoke": SMOKE_PATH,
        "regression": REGRESSION_PATH,
    }
    loaded: dict[str, dict[str, Any]] = {}
    if REVISION_PATTERN.fullmatch(support_revision):
        for label, path in expected_paths.items():
            try:
                identity = git_blob_identity(project_root, support_revision, path)
                raw = _git_bytes(project_root, support_revision, path)
                loaded[label] = json.loads(raw)
            except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
                errors.append(f"supporting_artifact_unresolvable:{label}")
                continue
            if canonical(supporting.get(label)) != canonical(identity):
                errors.append(f"supporting_artifact_identity:{label}")
    experiment_result: dict[str, Any] = {}
    smoke_result: dict[str, Any] = {}
    regression_result: dict[str, Any] = {}
    try:
        if "experiment" in loaded:
            experiment_result = validate_s8_experiment(
                loaded["experiment"],
                config=config,
                private_root=experiment_private_root,
                project_root=project_root,
                validation_revision=support_revision,
            )
    except (S8EvidenceValidationError, ValueError) as exc:
        errors.append(f"experiment:{exc}")
    try:
        if "runtime_smoke" in loaded:
            smoke_result = validate_s8_runtime_smoke(
                loaded["runtime_smoke"],
                private_closure_root=private_closure_root,
                project_root=project_root,
                validation_revision=support_revision,
            )
    except (S8ClosureValidationError, ValueError) as exc:
        errors.append(f"runtime_smoke:{exc}")
    try:
        if "regression" in loaded:
            regression_result = validate_s8_regression_evidence(
                loaded["regression"],
                private_closure_root=private_closure_root,
                project_root=project_root,
                validation_revision=support_revision,
            )
    except (S8ClosureValidationError, ValueError) as exc:
        errors.append(f"regression:{exc}")
    try:
        private_index = validate_private_closure_index(
            private_closure_root,
            public_summary=_mapping(payload.get("private_closure_evidence")),
        )
    except S8ClosureValidationError as exc:
        errors.append(f"private_closure:{exc}")
        private_index = {}

    expected_acceptance = {
        "S8-AC-01": bool(
            _mapping(experiment_result.get("recomputed_acceptance")).get("S8-AC-01")
        ),
        "S8-AC-02": bool(
            _mapping(experiment_result.get("recomputed_acceptance")).get("S8-AC-02")
        ),
        "S8-AC-03": bool(
            _mapping(experiment_result.get("recomputed_acceptance")).get("S8-AC-03")
        ),
        "S8-AC-04": bool(smoke_result.get("valid"))
        and bool(regression_result.get("valid"))
        and bool(private_index),
    }
    if canonical(payload.get("acceptance")) != canonical(expected_acceptance):
        errors.append("acceptance_projection")
    if not all(expected_acceptance.values()):
        errors.append("acceptance_not_passed")
    if payload.get("unresolved_blockers") != []:
        errors.append("unresolved_blockers")
    if not all(bool(value) for value in _mapping(payload.get("closure_checks")).values()):
        errors.append("closure_checks")
    if errors:
        raise S8ClosureValidationError(
            "s8_closure_invalid:" + ",".join(sorted(set(errors)))
        )
    return {
        "valid": True,
        "supporting_revision": support_revision,
        "acceptance": expected_acceptance,
        "fault_result_count": experiment_result["fault_result_count"],
        "soak_result_count": experiment_result["soak_result_count"],
        "regression_tests_passed": regression_result["tests_passed"],
        "private_closure_index": private_index,
    }


def _derive_regression_counts(suite_id: str, text: str) -> dict[str, Any]:
    if suite_id == "changed_file_lint":
        return {
            "passed": "All checks passed!" in text,
            "tests_passed": 0,
            "tests_skipped": 0,
        }
    if suite_id == "frontend_production_build":
        return {
            "passed": "built in" in text and "error" not in text.lower(),
            "tests_passed": 0,
            "tests_skipped": 0,
        }
    if suite_id == "control_panel":
        pytest_matches = re.findall(r"(?m)^(\d+) passed(?:, (\d+) skipped)?", text)
        vitest = re.search(r"Tests\s+(\d+) passed", text)
        python_passed = int(pytest_matches[0][0]) if pytest_matches else 0
        python_skipped = int(pytest_matches[0][1] or 0) if pytest_matches else 0
        frontend_passed = int(vitest.group(1)) if vitest else 0
        return {
            "passed": python_passed > 0 and frontend_passed > 0,
            "tests_passed": python_passed + frontend_passed,
            "tests_skipped": python_skipped,
            "python_tests_passed": python_passed,
            "frontend_tests_passed": frontend_passed,
        }
    matches = re.findall(r"(?m)^(\d+) passed(?:, (\d+) skipped)?", text)
    tests_passed = int(matches[-1][0]) if matches else 0
    tests_skipped = int(matches[-1][1] or 0) if matches else 0
    return {
        "passed": tests_passed > 0,
        "tests_passed": tests_passed,
        "tests_skipped": tests_skipped,
    }


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else []


def _read_mapping(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise S8ClosureValidationError(f"mapping_required:{path.name}")
    return payload


def _is_ancestor(root: Path, revision: str, descendant: str) -> bool:
    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", revision, descendant],
            cwd=root,
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )


def _git_bytes(root: Path, revision: str, path: Path) -> bytes:
    git_root = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    prefix = root.resolve().relative_to(git_root.resolve())
    return subprocess.run(
        ["git", "show", f"{revision}:{(prefix / path).as_posix()}"],
        cwd=git_root,
        capture_output=True,
        check=True,
    ).stdout
